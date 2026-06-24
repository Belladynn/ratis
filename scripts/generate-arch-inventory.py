#!/usr/bin/env python3
"""Generate docs/reference/ARCH_INVENTORY.md from all long-lived docs that follow the
``## ID — title · refs · STATUS`` convention (Batch A — inventory rework).

Sources scanned :
  - ``ARCH_*.md`` anywhere (tracked) — includes ``docs/arch/`` (PR phase A
    relocate, 2026-05) and historical root + per-service locations.
  - ``KNOWN_PROBLEMS.md`` at ``docs/known/`` (relocated phase A) — root path
    also accepted for back-compat / synthetic test repos.
  - ``DECISIONS_ACTED.md`` at ``docs/decisions/`` (relocated phase A) — root
    path also accepted for back-compat / synthetic test repos.

Sources explicitly excluded :
  - ``docs/superpowers/`` (specs/plans are transitory, distilled post-merge).
  - ``SESSION_LOG.md``, ``PRODUCT.md``, ``PRIVACY.md``, ``TRAINING.md``,
    ``PROD_CHECKLIST.md`` (now under ``docs/ops/``; narratif, hors convention).
  - ``AUDIT_*.md`` / ``docs/audits/`` (one-shot, pas long-vivant).
  - ``CLAUDE.md``, ``ORCHESTRATOR.md``, ``SA_*.md`` (now under ``docs/agents/``;
    refs agent, hors convention — excluded by basename).
  - ``ARCH_INVENTORY.md`` (self, now under ``docs/reference/``).

For each ``## <ID> — <titre> · <refs> · <STATUT>`` heading the script extracts :
  - ``> <TL;DR>`` (one-line summary on the next quote line)
  - ``> @tags: mots espacés``
  - ``> @subs: auto`` (computed = the H3 sections that follow until next H2)

Compliant IDs recognised : DA-N · KP-N · HSP-N · M-N (extensible to any
``[A-Z]+-N``).

Statuses recognised : ``LIVRÉ``, ``EN-COURS``, ``PLANIFIÉ``, ``DEPRECATED``.
A free suffix is allowed (e.g. ``LIVRÉ V1.1``).

Files with no compliant ``## ID —`` headings can also declare a **file-level
header** right after the H1 title (Batch B legacy migration) ::

    # <Title>
    > <TL;DR>
    > @tags: word1 word2 …
    > @status: <STATUS> [V0|V1...]
    > @subs: auto

When detected, the script emits a single entry per file with
``id = path.stem`` (e.g. ``ARCH_geo``), the status from ``@status:``, and
``@subs: auto`` resolved to the H2 sections that follow.

Files with neither compliant ``## ID —`` headings nor a file-level header
fall back to a bare LEGACY entry per file (ID = path stem, TL;DR = first
non-header line, no tags).

Output format : a short preamble, then one pipe-separated line per entity,
sorted by status bucket (EN-COURS → LIVRÉ → PLANIFIÉ → LEGACY → DEPRECATED),
then by ID alpha within each bucket. The pipe layout is grep-friendly (NOT a
markdown table), so agents can `grep KP-` / `grep n8n` / `grep EN-COURS`
and get useful matches without reading the file.

Agent rule (CLAUDE.md R28/R29) : read this index BEFORE any brainstorm/design
session; NEVER full-read it — always `Grep` on tag/ID/status.

Usage:
    python scripts/generate-arch-inventory.py          # regenerate
    python scripts/generate-arch-inventory.py --check  # CI mode: fail if stale
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
OUTPUT = REPO_ROOT / "docs" / "reference" / "ARCH_INVENTORY.md"

# Files explicitly NOT scanned even if they look like docs (narratif / agent
# refs / one-shot audits / superpowers transitory specs).
EXCLUDED_BASENAMES = {
    "ARCH_INVENTORY.md",
    "SESSION_LOG.md",
    "PRODUCT.md",
    "PRIVACY.md",
    "TRAINING.md",
    "PROD_CHECKLIST.md",
    "CLAUDE.md",
    "ORCHESTRATOR.md",
}

# Glob excludes (apply to any tracked path).
EXCLUDED_PATH_PREFIXES = (
    "docs/superpowers/",
    "docs/audits/",
    "docs/product/",
)


def _is_excluded(rel_path: str) -> bool:
    """Decide whether a tracked relative path should be skipped."""
    name = rel_path.rsplit("/", 1)[-1]
    if name in EXCLUDED_BASENAMES:
        return True
    if name.startswith("SA_") and name.endswith(".md"):
        return True  # SA_DEV.md, SA_EXPLORE.md, …
    if name.startswith("AUDIT_") and name.endswith(".md"):
        return True
    return any(rel_path.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def find_doc_files() -> list[Path]:
    """Find all tracked candidate docs via ``git ls-files``.

    We list a superset (ARCH_*.md anywhere + KNOWN_PROBLEMS.md +
    DECISIONS_ACTED.md at both their relocated ``docs/<cat>/`` path AND the
    historical root path for back-compat / synthetic test repos), then
    filter out excluded paths via ``_is_excluded``.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "ls-files",
                ":(glob)**/ARCH_*.md",
                "ARCH_*.md",
                "KNOWN_PROBLEMS.md",
                "DECISIONS_ACTED.md",
                "docs/known/KNOWN_PROBLEMS.md",
                "docs/decisions/DECISIONS_ACTED.md",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"ERROR: git ls-files failed: {e}", file=sys.stderr)
        return []

    results: list[Path] = []
    seen: set[str] = set()
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line or line in seen:
            continue
        seen.add(line)
        if _is_excluded(line):
            continue
        p = REPO_ROOT / line
        if not p.exists():
            continue
        results.append(p)
    return sorted(results, key=lambda p: p.as_posix())


# ---------------------------------------------------------------------------
# Parsing — new convention
# ---------------------------------------------------------------------------


# ``## DA-99 — titre · #534/#540 · LIVRÉ V1.1``
# Group 1 = ID, 2 = titre, 3 = refs, 4 = base status, 5 = optional suffix.
_HEADING_RE = re.compile(r"^##\s+([A-Z]+-\d+)\s+—\s+(.+?)\s+·\s+(.+?)\s+·\s+(LIVRÉ|EN-COURS|PLANIFIÉ|DEPRECATED)(.*)$")

# Any `## <ID>-<N> ...` that *starts* like a convention entry — used to detect
# malformed headings that should raise rather than be silently dropped.
_HEADING_PREFIX_RE = re.compile(r"^##\s+([A-Z]+-\d+)\s+—\s+(.*)$")

# `> @tags: word1 word2 ...`
_TAGS_RE = re.compile(r"^>\s*@tags:\s*(.*)$")

# `> @subs: auto` OR `> @subs: a · b · c`
_SUBS_RE = re.compile(r"^>\s*@subs:\s*(.*)$")

# `> @status: LIVRÉ V0` — only used by the file-level header (Batch B legacy
# migration). Section-level entries carry status in the `## … · STATUT`
# heading itself.
_STATUS_RE = re.compile(r"^>\s*@status:\s*(.*)$")

# `> <TL;DR>` — any quote line that is NOT a @-directive.
_QUOTE_RE = re.compile(r"^>\s?(.*)$")

# `### Heading` — counted as direct subs of a `##` block.
_H3_RE = re.compile(r"^###\s+(.+)$")

# Lines starting with H1 (title of file).
_H1_RE = re.compile(r"^#\s+(.+)$")


_STATUS_BUCKETS = ["EN-COURS", "LIVRÉ", "PLANIFIÉ", "LEGACY", "DEPRECATED"]
_STATUS_RANK = {b: i for i, b in enumerate(_STATUS_BUCKETS)}


class ConventionError(ValueError):
    """Raised when a heading looks like a convention entry but is malformed.

    R33 — better to fail loudly than silently drop the entry.
    """


def _normalise_pipe(text: str) -> str:
    """Replace literal ``|`` in user text by ``∣`` so it can't break the
    pipe-separated output."""
    return text.replace("|", "∣")


def _normalise_line(text: str) -> str:
    """Strip newlines and collapse whitespace so a TL;DR fits on one line."""
    return _normalise_pipe(" ".join(text.split()))


def _parse_heading_status(suffix: str) -> str:
    """Return the full status string (e.g. ``LIVRÉ V1.1``) from a heading.

    ``suffix`` is everything after the base status word. We strip trailing
    whitespace and prepend the base status — but the *base* is passed
    separately by the caller. Helper kept for readability.
    """
    s = suffix.strip()
    return s


def _looks_like_convention_heading(line: str) -> bool:
    """Does this ``## ID — ...`` line look like a *malformed* attempt at the
    new convention?

    True when the heading has an ID prefix AND either contains a ``·``
    separator OR mentions a status keyword — meaning the author tried to
    follow the convention but got the format wrong. Plain
    ``## DA-N — title (date)`` headings (legacy DECISIONS_ACTED.md style,
    no separator and no status word) are NOT flagged here — they're just
    skipped, and ``check-arch-convention.sh`` surfaces them as warnings
    during the migration period.
    """
    if not _HEADING_PREFIX_RE.match(line):
        return False
    has_sep = " · " in line
    has_status = any(kw in line for kw in ("LIVRÉ", "EN-COURS", "PLANIFIÉ", "DEPRECATED"))
    return has_sep or has_status


def _strip_yaml_frontmatter(text: str) -> str:
    """Remove a leading YAML frontmatter block (``---\\n...\\n---\\n``) if any.

    Several legacy ARCHs use Obsidian-style frontmatter for metadata. Without
    stripping it the LEGACY TL;DR fallback picks up `type: service-global`
    instead of the first real prose line.
    """
    if not text.startswith("---\n"):
        return text
    try:
        end = text.index("\n---\n", 4)
        return text[end + len("\n---\n") :]
    except ValueError:
        return text


def _status_bucket_from_full(status_full: str) -> str:
    """Extract the base status bucket from ``LIVRÉ V0`` / ``EN-COURS`` / ...

    Falls back to ``LEGACY`` if the value doesn't start with one of the
    known buckets.
    """
    s = status_full.strip()
    for bucket in ("LIVRÉ", "EN-COURS", "PLANIFIÉ", "DEPRECATED"):
        if s == bucket or s.startswith(bucket + " "):
            return bucket
    return "LEGACY"


def _parse_file_level_header(raw_lines: list[str], rel: str, file_stem: str) -> dict | None:
    """Detect the Batch B file-level header and return an entry dict.

    Pattern : a single H1 line, followed (after optional blank lines) by a
    contiguous quote block that contains AT LEAST ``@status:``. ``@tags:``,
    ``@subs:`` and a plain-text TL;DR line are also extracted when present.

    Returns ``None`` if no plausible header is found (caller falls back to
    the basic LEGACY summary). When a header LOOKS like an attempt but is
    missing ``@status``, we emit a stderr warning and return ``None`` so
    the author sees the issue without crashing the regeneration.
    """
    n = len(raw_lines)
    if n == 0:
        return None

    # Skip a YAML frontmatter (--- ... ---) if any so the H1 detection
    # operates on the real document body.
    i = 0
    if raw_lines[0].rstrip() == "---":
        for j in range(1, n):
            if raw_lines[j].rstrip() == "---":
                i = j + 1
                break
        else:
            return None

    # Find the H1. Skip blank lines and HTML comments (some legacy ARCHs use
    # `<!-- impl notes -->` blocks right after the frontmatter — we want to
    # land on the next H1 line, not abort detection).
    while i < n:
        line = raw_lines[i]
        stripped = line.strip()
        if stripped == "":
            i += 1
            continue
        if stripped.startswith("<!--"):
            # Skip until end of comment (inclusive). Comment can span lines.
            if "-->" in line:
                i += 1
                continue
            i += 1
            while i < n and "-->" not in raw_lines[i]:
                i += 1
            if i < n:
                i += 1  # skip the closing line
            continue
        break
    if i >= n:
        return None
    h1_line = raw_lines[i]
    if not _H1_RE.match(h1_line):
        return None
    h1_line_no = i + 1
    i += 1

    # Skip blank lines between H1 and the quote block.
    while i < n and raw_lines[i].strip() == "":
        i += 1

    # Collect contiguous quote lines.
    quote_block: list[str] = []
    while i < n and raw_lines[i].startswith(">"):
        quote_block.append(raw_lines[i])
        i += 1

    if not quote_block:
        return None

    # Parse the quote block.
    tldr_parts: list[str] = []
    tldr_done = False  # once we hit a @-directive, no more TL;DR lines
    tags = ""
    status_full = ""
    subs_directive = ""
    saw_any_directive = False

    for qline in quote_block:
        tag_m = _TAGS_RE.match(qline)
        subs_m = _SUBS_RE.match(qline)
        status_m = _STATUS_RE.match(qline)
        if tag_m:
            tags = tag_m.group(1).strip()
            saw_any_directive = True
            tldr_done = True
            continue
        if subs_m:
            subs_directive = subs_m.group(1).strip()
            saw_any_directive = True
            tldr_done = True
            continue
        if status_m:
            status_full = status_m.group(1).strip()
            saw_any_directive = True
            tldr_done = True
            continue
        # Plain quote line — TL;DR (potentially multi-line, joined by space).
        if tldr_done:
            continue
        qm = _QUOTE_RE.match(qline)
        if qm:
            candidate = qm.group(1).strip()
            if candidate:
                tldr_parts.append(candidate)

    # If the quote block has NO directive whatsoever, it's a plain prose
    # quote, not a file-level header. Skip silently.
    if not saw_any_directive:
        return None

    # If the author placed *some* directive but forgot @status, warn and
    # let the caller fall back to LEGACY. This is the most useful signal
    # for an incomplete migration.
    if not status_full:
        print(
            f"WARN: {rel}:{h1_line_no} — file-level header missing `@status` "
            f"directive (found {'@tags ' if tags else ''}"
            f"{'@subs' if subs_directive else ''} only); falling back to LEGACY",
            file=sys.stderr,
        )
        return None

    # @subs: auto => compute the H2s that follow the header.
    h2_collected: list[tuple[str, int]] = []
    k = i
    while k < n:
        ln = raw_lines[k]
        if ln.startswith("## ") and not ln.startswith("### "):
            # Strip leading "## " and trailing whitespace.
            h2_collected.append((ln[3:].strip(), k + 1))
        k += 1

    if subs_directive == "auto":
        subs = " · ".join(f"{name}(L{ln})" for name, ln in h2_collected) if h2_collected else ""
    elif subs_directive:
        subs = subs_directive
    else:
        subs = ""

    tldr_joined = " ".join(tldr_parts).strip()
    status_bucket = _status_bucket_from_full(status_full)

    return {
        "id": file_stem,
        "title": _normalise_line(file_stem),
        "refs": "",
        "status": _normalise_line(status_full),
        "status_bucket": status_bucket,
        "file": rel,
        "line": h1_line_no,
        "tldr": _normalise_line(tldr_joined),
        "tags": _normalise_line(tags),
        "subs": _normalise_line(subs),
    }


def parse_file(path: Path) -> list[dict]:
    """Parse one doc file, return a list of entry dicts.

    If no compliant ``## ID —`` heading is found, return a single LEGACY
    entry summarising the file.

    Line numbers reported in the index reflect the *raw* file (no frontmatter
    stripping) so a human can ``Read offset=line`` and land on the right spot.
    The LEGACY TL;DR fallback uses a frontmatter-stripped view to find a
    sensible summary line.
    """
    rel = path.relative_to(REPO_ROOT).as_posix()
    raw_text = path.read_text(encoding="utf-8")
    raw_lines = raw_text.splitlines()

    entries: list[dict] = []
    i = 0
    n = len(raw_lines)
    while i < n:
        line = raw_lines[i]
        m = _HEADING_RE.match(line)
        if not m:
            # Detect a malformed `## ID — ...` that doesn't match the full
            # convention — R33 : raise rather than skip silently.
            if _looks_like_convention_heading(line):
                pref = _HEADING_PREFIX_RE.match(line)
                bad_id = pref.group(1) if pref else "?"
                raise ConventionError(
                    f"{rel}:{i + 1} — {bad_id} : section heading does not match "
                    f"`## <ID> — <titre> · <refs> · <STATUT>` ({line.rstrip()!r})"
                )
            i += 1
            continue

        entry_id = m.group(1)
        title = m.group(2).strip()
        refs = m.group(3).strip()
        base_status = m.group(4).strip()
        suffix = _parse_heading_status(m.group(5))
        status = f"{base_status} {suffix}".strip()

        heading_line_no = i + 1  # 1-indexed for human readers

        # Parse the metadata quote block immediately following the heading.
        tldr = ""
        tags = ""
        subs_directive = ""
        # First quote line = TL;DR (any quote line that's not a @-directive)
        j = i + 1
        # Skip blank lines between heading and quote
        while j < n and raw_lines[j].strip() == "":
            j += 1
        # Capture consecutive quote lines
        quote_block: list[str] = []
        while j < n and raw_lines[j].startswith(">"):
            quote_block.append(raw_lines[j])
            j += 1

        for qline in quote_block:
            tag_m = _TAGS_RE.match(qline)
            subs_m = _SUBS_RE.match(qline)
            if tag_m:
                tags = tag_m.group(1).strip()
                continue
            if subs_m:
                subs_directive = subs_m.group(1).strip()
                continue
            # Plain TL;DR line. Take the first non-empty.
            if not tldr:
                qm = _QUOTE_RE.match(qline)
                if qm:
                    candidate = qm.group(1).strip()
                    if candidate:
                        tldr = candidate

        # Compute subs : scan H3 between this H2 and the next H2 (or EOF).
        h3_collected: list[tuple[str, int]] = []
        k = j
        while k < n:
            ln = raw_lines[k]
            if ln.startswith("## ") and not ln.startswith("### "):
                break
            h3m = _H3_RE.match(ln)
            if h3m:
                h3_collected.append((h3m.group(1).strip(), k + 1))
            k += 1

        if subs_directive == "auto":
            subs = " · ".join(f"{name}(L{ln})" for name, ln in h3_collected) if h3_collected else ""
        else:
            # User-provided list — keep verbatim.
            subs = subs_directive

        entries.append(
            {
                "id": entry_id,
                "title": _normalise_line(title),
                "refs": _normalise_line(refs),
                "status": _normalise_line(status),
                "status_bucket": base_status,
                "file": rel,
                "line": heading_line_no,
                "tldr": _normalise_line(tldr),
                "tags": _normalise_line(tags),
                "subs": _normalise_line(subs),
            }
        )
        i = j  # continue scan past the quote block

    if entries:
        return entries

    # ---- File-level header (Batch B — legacy migration) ----------------
    # Some legacy ARCHs declare metadata via a header block right after the
    # H1 title :
    #
    #     # <Title>
    #     > <TL;DR>
    #     > @tags: word1 word2 …
    #     > @status: <STATUS> [V0|V1...]
    #     > @subs: auto
    #
    # When this pattern is detected we emit one entry per file with
    # ``id = path.stem`` instead of falling back to the bare LEGACY summary.
    file_level = _parse_file_level_header(raw_lines, rel, path.stem)
    if file_level is not None:
        return [file_level]

    # ---- LEGACY fallback ------------------------------------------------
    # No compliant heading → 1 entry summarising the file.
    legacy_id = path.stem  # e.g. ARCH_geo → "ARCH_geo"
    # TL;DR = first non-header non-quote non-empty line. Quote-block intros
    # (`> Status: …`) make a good fallback summary too. We skip pure
    # horizontal rules (`---`) and YAML frontmatter delimiters.
    tldr = ""
    summary_lines = _strip_yaml_frontmatter(raw_text).splitlines()
    for ln in summary_lines:
        s = ln.strip()
        if not s:
            continue
        if _H1_RE.match(ln) or ln.startswith("## ") or ln.startswith("### "):
            continue
        if set(s) <= {"-"} or set(s) <= {"="}:
            continue  # horizontal rule
        if ln.startswith(">"):
            qm = _QUOTE_RE.match(ln)
            if qm and qm.group(1).strip():
                tldr = qm.group(1).strip()
                break
            continue
        tldr = s
        break

    return [
        {
            "id": legacy_id,
            "title": legacy_id,
            "refs": "",
            "status": "LEGACY",
            "status_bucket": "LEGACY",
            "file": rel,
            "line": 1,
            "tldr": _normalise_line(tldr) or "(non migré — convention DA-N à appliquer)",
            "tags": "",
            "subs": "",
        }
    ]


# ---------------------------------------------------------------------------
# Aggregation + rendering
# ---------------------------------------------------------------------------


def collect_entries() -> list[dict]:
    """Scan all candidate docs and return a flat list of entries."""
    files = find_doc_files()
    out: list[dict] = []
    for f in files:
        out.extend(parse_file(f))
    return out


def sort_entries(entries: list[dict]) -> list[dict]:
    """Sort by status bucket (EN-COURS → LIVRÉ → PLANIFIÉ → LEGACY →
    DEPRECATED), then by ID alpha within a bucket."""

    def _key(e: dict) -> tuple[int, str]:
        return (_STATUS_RANK.get(e["status_bucket"], 99), e["id"])

    return sorted(entries, key=_key)


def _format_line(e: dict) -> str:
    """Render one entry as a pipe-separated line.

    Layout : ``ID | STATUT | file:line | tags | TL;DR``.
    Subs are omitted from the grep line (they bloat width without helping
    grep). To inspect subs the agent opens the source file at ``file:line``.
    """
    return " | ".join(
        [
            e["id"],
            e["status"],
            f"{e['file']}:{e['line']}",
            e["tags"],
            e["tldr"],
        ]
    )


def generate() -> str:
    entries = sort_entries(collect_entries())
    by_bucket: dict[str, int] = {}
    for e in entries:
        by_bucket[e["status_bucket"]] = by_bucket.get(e["status_bucket"], 0) + 1

    summary_parts = [f"{by_bucket.get(b, 0)} {b}" for b in _STATUS_BUCKETS if by_bucket.get(b, 0)]
    summary = " · ".join(summary_parts) if summary_parts else "0 entry"

    lines: list[str] = [
        "# Ratis Doc Inventory",
        "",
        "> Auto-generated — do not edit. Regenerate: `python scripts/generate-arch-inventory.py`.",
        "> CI checks freshness on every PR (`.github/workflows/doc-inventories.yml`).",
        "> Convention : CLAUDE.md R41 — every `## <ID> — <titre> · <refs> · <STATUT>` heading in",
        "> tracked ARCH_*.md / docs/known/KNOWN_PROBLEMS.md / docs/decisions/DECISIONS_ACTED.md is indexed below.",
        "",
        "> Agent rule (R29) : NEVER full-read this file — always `Grep` by tag/ID/status,",
        "> then open the source file at the indicated line.",
        "",
        f"**{len(entries)} entries** ({summary})",
        "",
        "Format : `ID | STATUT | file:line | tags espacés | TL;DR`",
        "",
        "ID | STATUT | FICHIER:LIGNE | TAGS | TL;DR",
        "---+--------+---------------+------+------",
    ]
    for e in entries:
        lines.append(_format_line(e))

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(check_only: bool = False) -> int:
    new_content = generate()
    if check_only:
        if not OUTPUT.exists():
            print(f"ERROR: {OUTPUT.name} missing. Run scripts/generate-arch-inventory.py", file=sys.stderr)
            return 1
        current = OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n")
        if current != new_content:
            print(
                f"ERROR: {OUTPUT.name} is out of date. Run scripts/generate-arch-inventory.py",
                file=sys.stderr,
            )
            import difflib

            diff = difflib.unified_diff(
                current.splitlines(keepends=True)[:80],
                new_content.splitlines(keepends=True)[:80],
                fromfile=f"committed {OUTPUT.name}",
                tofile=f"regenerated {OUTPUT.name}",
                n=3,
            )
            for line in diff:
                print(line, end="", file=sys.stderr)
            return 1
        print(f"OK: {OUTPUT.name} is up to date.")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="\n") as f:
        f.write(new_content)
    size = OUTPUT.stat().st_size
    count = sum(1 for ln in new_content.splitlines() if " | " in ln) - 1  # minus header
    print(f"Wrote {OUTPUT.name} ({size} bytes, {count} entries).")
    return 0


if __name__ == "__main__":
    check = "--check" in sys.argv
    sys.exit(main(check_only=check))
