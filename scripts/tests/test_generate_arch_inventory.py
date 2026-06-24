"""Tests for scripts/generate-arch-inventory.py (Batch A — inventory rework).

Covers the new pipe-separated inventory format:
- Compliant `## ID — title · refs · STATUS` sections parsed (TL;DR, @tags, @subs auto).
- LEGACY fallback for files without compliant sections.
- Excluded files (PRODUCT.md, SESSION_LOG.md, docs/superpowers/**, etc.) absent.
- Status sort order : EN-COURS → LIVRÉ → PLANIFIÉ → LEGACY → DEPRECATED.
- `--check` mode : exit 0 fresh, exit 1 stale.
"""

from __future__ import annotations

import importlib.util
import itertools
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "generate-arch-inventory.py"
_spec = importlib.util.spec_from_file_location("gen_arch", _SCRIPT)
gen = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(gen)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Fixtures : tmp git repo with a handful of docs
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Initialise a fake repo with ARCH/KNOWN/DA/excluded files, then point
    the script at it via monkeypatch of REPO_ROOT + OUTPUT."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")

    monkeypatch.setattr(gen, "REPO_ROOT", repo.resolve())
    monkeypatch.setattr(gen, "OUTPUT", (repo / "ARCH_INVENTORY.md").resolve())
    return repo


def _write_and_track(repo: Path, rel: str, content: str) -> None:
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    _git(repo, "add", rel)


# ---------------------------------------------------------------------------
# Synthetic doc fragments
# ---------------------------------------------------------------------------


CONFORMING_ARCH = """\
# ARCH conforming example

Some intro.

## DA-99 — exemple démo · #534/#540 · LIVRÉ V1.1
> Pipeline démo qui illustre la convention pipe-séparée du batch A.
> @tags: demo n8n approval audit-V1
> @subs: auto

### Architecture
Texte.

### Décisions actées
Texte.

#### Sous-sous-section ignorée
On ignore le niveau H4.

### Reste à faire
Texte.

## DA-100 — autre · PR-ref · EN-COURS
> Section en cours pour tester le tri par statut.
> @tags: in-progress test
> @subs: auto

### Plan
Plan.
"""


LEGACY_ARCH = """\
# ARCH_legacy non-migré

Cet ARCH n'a pas encore la convention DA-N. Il doit ressortir en LEGACY.

## Section libre 1
Texte.

## Section libre 2
Texte.
"""


EXCLUDED_PRODUCT = """\
# PRODUCT

Narratif produit — ne doit JAMAIS être dans l'inventaire.

## DA-77 — fake · PR · LIVRÉ
> Ne doit pas être pris en compte.
> @tags: x
> @subs: auto
"""


KNOWN_PROBLEMS_SNIPPET = """\
# KNOWN_PROBLEMS

Catalogue.

## KP-24 — R2/DB sync crash safety · #321 · PLANIFIÉ
> R2 upload + DB insert can desync on crash; need crash-safety pattern.
> @tags: r2 db sync crash celery commit-per-row
> @subs: auto

### Symptômes
Texte.

### Workaround
Texte.
"""


DECISIONS_ACTED_SNIPPET = """\
# DECISIONS_ACTED

## DA-50 — Référral cap journalier · #210 · LIVRÉ
> Cap quotidien sur le referral pour limiter le farming.
> @tags: referral cap anti-fraud
> @subs: auto

### Contexte
Texte.

### Décision
Texte.

## DA-51 — Vieille décision · n/a · DEPRECATED
> Ancienne décision dépréciée — exemple pour le tri DEPRECATED.
> @tags: legacy
> @subs: auto

### Contexte
Texte.
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_conforming_arch_extracts_pipe_line(tmp_repo: Path) -> None:
    _write_and_track(tmp_repo, "ARCH_demo.md", CONFORMING_ARCH)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    entries = gen.collect_entries()
    da99 = next(e for e in entries if e["id"] == "DA-99")
    assert da99["status"] == "LIVRÉ V1.1"
    assert da99["file"] == "ARCH_demo.md"
    assert da99["line"] == 5  # `## DA-99` is line 5 (1-indexed)
    assert "demo" in da99["tags"]
    assert "n8n" in da99["tags"]
    assert da99["tldr"].startswith("Pipeline démo qui illustre")
    # @subs: auto should resolve to the H3s under this H2 (not the H4)
    subs_str = da99["subs"]
    assert "Architecture" in subs_str
    assert "Décisions actées" in subs_str
    assert "Reste à faire" in subs_str
    assert "Sous-sous-section" not in subs_str  # H4 ignored


def test_legacy_arch_fallback(tmp_repo: Path) -> None:
    _write_and_track(tmp_repo, "ARCH_legacy.md", LEGACY_ARCH)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    entries = gen.collect_entries()
    legacy = [e for e in entries if e["status"] == "LEGACY"]
    assert len(legacy) == 1
    assert legacy[0]["id"] == "ARCH_legacy"
    assert legacy[0]["file"] == "ARCH_legacy.md"
    assert legacy[0]["tags"] == ""
    # TL;DR = first non-header content line
    assert "n'a pas encore" in legacy[0]["tldr"]


def test_excluded_files_not_scanned(tmp_repo: Path) -> None:
    bad_section = "## DA-{n} — x · y · LIVRÉ\n> bad\n> @tags: x\n> @subs: auto\n"
    _write_and_track(tmp_repo, "PRODUCT.md", EXCLUDED_PRODUCT)
    _write_and_track(tmp_repo, "SESSION_LOG.md", "# SESSION_LOG\n\n" + bad_section.format(n=1))
    _write_and_track(tmp_repo, "docs/superpowers/specs/2026-spec.md", bad_section.format(n=2))
    _write_and_track(tmp_repo, "AUDIT_2026-05-17.md", bad_section.format(n=3))
    _write_and_track(tmp_repo, "ARCH_real.md", CONFORMING_ARCH)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    entries = gen.collect_entries()
    ids = {e["id"] for e in entries}
    assert "DA-99" in ids  # from ARCH_real
    assert "DA-77" not in ids  # PRODUCT.md excluded
    assert "DA-1" not in ids  # SESSION_LOG excluded
    assert "DA-2" not in ids  # docs/superpowers excluded
    assert "DA-3" not in ids  # AUDIT_* excluded


def test_status_sort_order(tmp_repo: Path) -> None:
    _write_and_track(tmp_repo, "ARCH_demo.md", CONFORMING_ARCH)
    _write_and_track(tmp_repo, "KNOWN_PROBLEMS.md", KNOWN_PROBLEMS_SNIPPET)
    _write_and_track(tmp_repo, "DECISIONS_ACTED.md", DECISIONS_ACTED_SNIPPET)
    _write_and_track(tmp_repo, "ARCH_legacy.md", LEGACY_ARCH)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    entries = gen.collect_entries()
    sorted_entries = gen.sort_entries(entries)
    statuses = [e["status_bucket"] for e in sorted_entries]
    # The order should be EN-COURS → LIVRÉ → PLANIFIÉ → LEGACY → DEPRECATED
    rank = {b: i for i, b in enumerate(["EN-COURS", "LIVRÉ", "PLANIFIÉ", "LEGACY", "DEPRECATED"])}
    for a, b in itertools.pairwise(statuses):
        assert rank[a] <= rank[b], f"sort violated: {a} before {b}"


def test_generate_produces_pipe_separated_lines(tmp_repo: Path) -> None:
    _write_and_track(tmp_repo, "ARCH_demo.md", CONFORMING_ARCH)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    content = gen.generate()
    # Preamble present
    assert "Auto-generated" in content or "auto-régénéré" in content
    # Header row with pipe separators (we ship a single pipe-delim format)
    assert "ID" in content
    assert "STATUT" in content
    assert "TL;DR" in content
    # A data line for DA-99
    da99_lines = [ln for ln in content.splitlines() if ln.startswith("DA-99 ")]
    assert len(da99_lines) == 1
    line = da99_lines[0]
    # Pipe-separated, contains tags and TL;DR
    assert " | " in line
    assert "LIVRÉ V1.1" in line
    assert "ARCH_demo.md:5" in line
    assert "demo" in line


def test_check_mode_returns_0_when_fresh(tmp_repo: Path) -> None:
    _write_and_track(tmp_repo, "ARCH_demo.md", CONFORMING_ARCH)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    # Write current content
    assert gen.main(check_only=False) == 0
    # Now --check should pass
    assert gen.main(check_only=True) == 0


def test_check_mode_returns_1_when_stale(tmp_repo: Path) -> None:
    _write_and_track(tmp_repo, "ARCH_demo.md", CONFORMING_ARCH)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    # First write
    assert gen.main(check_only=False) == 0
    # Mutate the source → inventory becomes stale
    (tmp_repo / "ARCH_demo.md").write_text(
        CONFORMING_ARCH + "\n## DA-101 — extra · PR · LIVRÉ\n> Nouveau.\n> @tags: x\n> @subs: auto\n\n### S\n",
        encoding="utf-8",
    )
    _git(tmp_repo, "add", "ARCH_demo.md")
    assert gen.main(check_only=True) == 1


def test_malformed_section_raises_explicit_error(tmp_repo: Path) -> None:
    """R33 — refuse to silently accept a half-formed `## DA-X — title` line.

    A `## DA-X — title` without trailing ` · STATUS` is a mistake the author
    must fix. The script must point at file:line in the error message.
    """
    bad = """\
# ARCH bad

## DA-50 — incomplet · #123 · STATUS_INCONNU
> Statut hors vocabulaire — pattern attendu LIVRÉ/EN-COURS/PLANIFIÉ/DEPRECATED.
> @tags: x
> @subs: auto

### S
"""
    _write_and_track(tmp_repo, "ARCH_bad.md", bad)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    with pytest.raises(gen.ConventionError) as exc:
        gen.collect_entries()
    msg = str(exc.value)
    assert "ARCH_bad.md" in msg
    assert "DA-50" in msg


# ---------------------------------------------------------------------------
# File-level header (Batch B — legacy migration)
#
# A legacy ARCH file can declare its own metadata via a header block placed
# right after the H1 title:
#
#     # <Title>
#     > <TL;DR>
#     > @tags: word1 word2 …
#     > @status: <STATUS> [V0|V1...]
#     > @subs: auto
#
# When this pattern is detected, the script emits a single entry for the
# whole file with `id = path.stem` (e.g. `ARCH_geo`).
# ---------------------------------------------------------------------------


FILE_LEVEL_ARCH = """\
# ARCH_geo

> Géocodage stores via PostGIS + ratis_core.geo. Remplace l'ancien
> stack OSM/Overpass pour la proximité magasins.
> @tags: geo postgis store-resolution proximity ratis_core osm-deprecated
> @status: LIVRÉ V0
> @subs: auto

## Architecture
Texte.

## Décisions
Texte.

### Notes internes
On ignore.
"""


FILE_LEVEL_MALFORMED_ARCH = """\
# ARCH_broken

> Header sans la directive @status — donc malformé.
> @tags: x y z
> @subs: auto

## Quelque chose
Texte.
"""


FILE_LEVEL_WITH_SECTIONS = """\
# ARCH_mixed

> Fichier qui a un header file-level ET aussi des sections conformantes.
> @tags: mixed file-level section
> @status: EN-COURS
> @subs: auto

## DA-200 — section conformante · #999 · LIVRÉ V1.0
> Sections conformantes prennent priorité quand elles existent.
> @tags: section level
> @subs: auto

### Sous-section
Texte.
"""


def test_file_level_header_recognised(tmp_repo: Path) -> None:
    """A legacy ARCH with a file-level header should produce a rich entry.

    ID = filename stem, status = `@status` value, tags = `@tags` value,
    TL;DR = first plain quote line, subs auto = the H2s that follow.
    """
    _write_and_track(tmp_repo, "ARCH_geo.md", FILE_LEVEL_ARCH)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    entries = gen.collect_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e["id"] == "ARCH_geo"
    assert e["status"] == "LIVRÉ V0"
    assert e["status_bucket"] == "LIVRÉ"
    assert e["file"] == "ARCH_geo.md"
    assert e["line"] == 1
    assert "geo" in e["tags"]
    assert "postgis" in e["tags"]
    assert e["tldr"].startswith("Géocodage stores via PostGIS")
    # Multi-line quote TL;DR should be collapsed
    assert "ratis_core.geo" in e["tldr"]
    # @subs: auto computes H2s following the header
    assert "Architecture" in e["subs"]
    assert "Décisions" in e["subs"]
    assert "Notes internes" not in e["subs"]  # H3 ignored


def test_file_level_header_malformed_falls_back_to_legacy(tmp_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A file with @tags but no @status is *not* a full file-level header.

    It should fall back to the LEGACY summary (status='LEGACY') and emit a
    warning on stderr so the author knows to complete the header.
    """
    _write_and_track(tmp_repo, "ARCH_broken.md", FILE_LEVEL_MALFORMED_ARCH)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    entries = gen.collect_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e["status"] == "LEGACY"
    assert e["id"] == "ARCH_broken"
    captured = capsys.readouterr()
    # Warning surfaced for the author
    assert "ARCH_broken.md" in captured.err
    assert "@status" in captured.err


def test_file_level_header_yields_to_section_entries(tmp_repo: Path) -> None:
    """If a file has BOTH a file-level header AND conforming `## ID — ...`
    sections, the section entries take priority (file-level header is just
    a fallback when sections are absent — typical legacy case).
    """
    _write_and_track(tmp_repo, "ARCH_mixed.md", FILE_LEVEL_WITH_SECTIONS)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    entries = gen.collect_entries()
    ids = {e["id"] for e in entries}
    # Section entry is present
    assert "DA-200" in ids
    # File-level entry must NOT be added on top (we don't duplicate)
    assert "ARCH_mixed" not in ids


FILE_LEVEL_WITH_HTML_COMMENT = """\
---
type: sub-arch
status: in-progress
---

<!-- 2026-04-24 · V0 frontend implemented (feature/foo).
     Notes d'implémentation diverses qui parlent du contexte.
     - point 1
     - point 2
-->


# ARCH_with_comment

> Header file-level qui DOIT être détecté même si un commentaire HTML précède le H1.
> @tags: comment html h1-skip-comment
> @status: EN-COURS
> @subs: auto

## Section A
Texte.
"""


def test_file_level_header_skips_html_comment_before_h1(tmp_repo: Path) -> None:
    """Real-world legacy ARCHs put `<!-- impl notes -->` blocks right after
    the YAML frontmatter (cf ARCH_scan_history.md). The parser must skip
    them and still detect the H1 + the file-level header that follows.
    """
    _write_and_track(tmp_repo, "ARCH_with_comment.md", FILE_LEVEL_WITH_HTML_COMMENT)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    entries = gen.collect_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e["id"] == "ARCH_with_comment"
    assert e["status"] == "EN-COURS"
    assert "comment" in e["tags"]
    assert "Header file-level" in e["tldr"]


def test_legacy_arch_without_header_still_fallback(tmp_repo: Path) -> None:
    """Regression : an ARCH without any header at all still gets the
    original LEGACY fallback (so we don't break existing behaviour for
    files not yet migrated).
    """
    _write_and_track(tmp_repo, "ARCH_legacy.md", LEGACY_ARCH)
    _git(tmp_repo, "commit", "-q", "-m", "init")

    entries = gen.collect_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e["id"] == "ARCH_legacy"
    assert e["status"] == "LEGACY"
    assert e["status_bucket"] == "LEGACY"
    assert "n'a pas encore" in e["tldr"]
