"""Docs search & retrieval tools — phase C of the agentic-docs initiative.

Exposes 4 typed tools to Claude Code agents (all ``ops``-scoped, read-only) :

* ``docs_search(query, top_k=5)``           — keyword grep over the inventory.
* ``docs_get(id)``                          — full body of one section.
* ``docs_find(status=, tags=, file_glob=)`` — structured filter.
* ``docs_list_files()``                     — inventory of indexed files.

Backend
-------
Two backends, picked automatically per call :

* **Hybrid (vector + keyword)** when a fresh ``.docs-vector-index.db`` is
  present (built once by `docs_reindex` or the session-start hook). See
  :mod:`docs_vector` — runs cosine similarity over bge-m3 embeddings and
  the keyword score in parallel, then re-ranks with weights
  ``0.7 * vector + 0.3 * keyword`` to favour semantic matches while still
  rewarding exact-tag hits.
* **Keyword-only (V1 backend, kept as fallback)** when the index is
  missing, stale, or the embedder isn't installed. Pure grep over
  ``ARCH_INVENTORY.md`` (see :mod:`docs_index`).

The public surface (`docs_search`, `docs_get`, `docs_find`,
`docs_list_files`) is identical across backends — agents don't change a
single call when the index is built or invalidated.

Why these tools (vs. raw Grep / Read)
-------------------------------------
The orchestrator (CLAUDE.md R28/R29) tells agents to never full-read the
inventory or large ARCH files. These tools wrap the discipline into typed
calls : Claude asks "give me HSP-3" and gets the bounded section without
ever knowing the path or the line range. The grep heuristic lives in one
place, so we can swap it for embeddings without touching agents.

Return shapes
-------------
All tools return JSON-serialisable dicts (``.model_dump()`` on the typed
Pydantic models from :mod:`docs_index`). This matches the
``json.dumps(envelope, default=str)`` contract in
``agent_mcp.server.build_mcp_server`` and what every existing tool module
already does.

References
----------
* ARCH_agent_mcp.md (this module documented in § docs-mcp).
* CLAUDE.md R29 (never full-read large docs) ; R41 (inventory convention).
"""

from __future__ import annotations

import fnmatch
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ..server import TOOLS_REGISTRY, register_tool
from . import docs_vector
from .docs_index import (
    DEFAULT_SOURCES,
    Entry,
    FileInfo,
    Section,
    _repo_root,
    load_corpus,
    load_inventory,
)

# Weights of the hybrid rerank — `vector_weight + keyword_weight == 1.0`
# is not enforced but recommended. 0.7/0.3 favours semantic matches while
# still rewarding exact tag/id hits ; see ARCH_agent_mcp.md § phase D.
VECTOR_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3

# How many candidates to ask each backend for before the rerank fusion.
# Larger than `top_k` to give the fusion a useful pool when the two
# rankings disagree (typical case for semantic queries).
HYBRID_CANDIDATE_POOL = 20


# -- search ----------------------------------------------------------------


def _score_entry(entry: Entry, terms: list[str]) -> int:
    """Return a relevance score for ``entry`` against the lowercased ``terms``.

    Heuristic :

    * +10 per distinct term that appears anywhere in the search corpus
      (id + status + tags + tldr + file_path), lowercased substring match.
    * +5  per term that matches a tag exactly (tags are the curated keyword
      surface — exact tag hits are stronger signals than tldr hits).
    * +3  per term that appears in the ``id``.

    Score 0 means "no term matched" — those entries are filtered out by
    the caller.
    """
    corpus = " ".join(
        [
            entry.id,
            entry.status,
            entry.file_path,
            " ".join(entry.tags),
            entry.tldr,
        ]
    ).lower()
    lower_tags = {t.lower() for t in entry.tags}
    lower_id = entry.id.lower()

    score = 0
    for term in terms:
        if not term:
            continue
        if term in corpus:
            score += 10
        if term in lower_tags:
            score += 5
        if term in lower_id:
            score += 3
    return score


def _keyword_search(
    query: str,
    top_k: int,
    *,
    entries: list[Entry] | None = None,
) -> list[tuple[Entry, int]]:
    """V1 backend — keyword grep over the inventory. Returns ``(entry, score)``
    pairs sorted by score desc, ties broken by ID alpha.

    When ``entries`` is provided, search over that set instead of the
    inventory — used by the multi-source path (the corpus is already loaded
    by the caller so we don't re-IO).
    """
    terms = [t.lower() for t in query.split() if t.strip()]
    if not terms:
        return []
    pool = entries if entries is not None else load_inventory()
    scored: list[tuple[Entry, int]] = []
    for e in pool:
        s = _score_entry(e, terms)
        if s > 0:
            scored.append((e, s))
    scored.sort(key=lambda pair: (-pair[1], pair[0].id))
    return scored[:top_k]


# -- multi-source filters (session-context-bridge extension) -------------


def _parse_iso_to_utc(stamp: str) -> datetime | None:
    """Tolerant ISO-8601 → aware UTC datetime. Returns None on failure."""
    try:
        dt = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _apply_filters(
    entries: list[Entry],
    *,
    sources: list[str] | None,
    file_pattern: str | None,
    freshness_days: int | None,
    status_filter: str | None,
) -> list[Entry]:
    """Apply the optional metadata filters to a list of entries.

    All filters AND-combine. Entries without an ``indexed_at`` are kept
    when ``freshness_days`` is set — the user-MEMORY parser sets it to
    ``None`` on purpose ("timeless") and we don't want to drop them.
    """
    if not entries:
        return entries
    cutoff: datetime | None = None
    if freshness_days is not None and freshness_days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=freshness_days)

    sources_set = {s.lower() for s in sources} if sources else None
    status_needle = status_filter.lower() if status_filter else None

    out: list[Entry] = []
    for e in entries:
        if sources_set is not None and e.source.lower() not in sources_set:
            continue
        if file_pattern is not None and not fnmatch.fnmatch(e.file_path, file_pattern):
            continue
        if status_needle is not None and status_needle not in e.status.lower():
            continue
        if cutoff is not None and e.indexed_at:
            dt = _parse_iso_to_utc(e.indexed_at)
            if dt is not None and dt < cutoff:
                continue
        out.append(e)
    return out


def _multi_source_search(
    query: str,
    top_k: int,
    *,
    sources: list[str] | None,
    file_pattern: str | None,
    freshness_days: int | None,
    status_filter: str | None,
) -> list[Entry]:
    """Keyword search over the multi-source corpus with metadata filters.

    Used when at least one filter is set. We always go through keyword on
    this path — the vector index is built from ARCH_INVENTORY only today
    (extending it to multi-source is V2). The keyword backend is good enough
    for the SessionStart bridge.
    """
    corpus = load_corpus()
    filtered = _apply_filters(
        corpus,
        sources=sources,
        file_pattern=file_pattern,
        freshness_days=freshness_days,
        status_filter=status_filter,
    )
    return [e for e, _ in _keyword_search(query, top_k, entries=filtered)]


def _normalise_scores(pairs: list[tuple[Entry, float]]) -> dict[str, float]:
    """Map ``id -> normalised score in [0, 1]`` by max-scaling.

    Empty input → empty dict. A single positive score → ``1.0`` (otherwise
    the rerank weights become meaningless). All-zero input → all-zero dict.
    """
    if not pairs:
        return {}
    max_score = max(score for _, score in pairs)
    if max_score <= 0:
        return {e.id: 0.0 for e, _ in pairs}
    return {e.id: float(score) / float(max_score) for e, score in pairs}


def _hybrid_search(query: str, top_k: int) -> list[Entry]:
    """Hybrid backend — run vector + keyword in parallel and re-rank.

    The fusion picks a candidate pool of size :data:`HYBRID_CANDIDATE_POOL`
    from EACH backend, normalises the two scores to ``[0, 1]`` via
    max-scaling, then takes the weighted sum
    (:data:`VECTOR_WEIGHT` · v + :data:`KEYWORD_WEIGHT` · k).

    An entry that appears in only one ranking still scores via that
    ranking's weight (the missing side contributes 0). This is the
    documented "vector or keyword pulls the entry up" behaviour from the
    phase-D plan.
    """
    pool = max(top_k, HYBRID_CANDIDATE_POOL)
    vector_pairs = docs_vector.search(query, top_k=pool)
    keyword_pairs = _keyword_search(query, top_k=pool)

    if not vector_pairs and not keyword_pairs:
        return []

    vec_norm = _normalise_scores([(e, s) for e, s in vector_pairs])
    kw_norm = _normalise_scores([(e, float(s)) for e, s in keyword_pairs])

    # Union the entries — preserve a single source of truth per id.
    entries_by_id: dict[str, Entry] = {}
    for e, _ in vector_pairs:
        entries_by_id[e.id] = e
    for e, _ in keyword_pairs:
        entries_by_id.setdefault(e.id, e)

    fused: list[tuple[Entry, float]] = []
    for entry_id, entry in entries_by_id.items():
        v = vec_norm.get(entry_id, 0.0)
        k = kw_norm.get(entry_id, 0.0)
        fused.append((entry, VECTOR_WEIGHT * v + KEYWORD_WEIGHT * k))

    fused.sort(key=lambda pair: (-pair[1], pair[0].id))
    return [e for e, _ in fused[:top_k]]


def docs_search(
    query: str,
    top_k: int = 5,
    sources: list[str] | None = None,
    file_pattern: str | None = None,
    freshness_days: int | None = None,
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid (vector + keyword) search over the doc corpus. Read-only. Scope: ops.

    Args:
        query : free-form text — natural language queries are encouraged
            (the vector backend embeds them semantically). Whitespace-split
            and matched case-insensitively for the keyword half. An empty
            query returns an empty list.
        top_k : max entries to return (capped at 50 to keep agent context
            light). Pass a larger value if you really want all matches.
        sources : restrict to a subset of named sources (e.g.
            ``["arch_inventory", "postmortems"]``). Default = no restriction.
            See :data:`docs_index.DEFAULT_SOURCES` for the list of names.
        file_pattern : fnmatch glob on ``file_path`` (post-search filter).
        freshness_days : drop entries whose ``indexed_at`` is older than
            this. Entries with ``indexed_at = None`` are always kept
            (timeless — e.g. user MEMORY).
        status_filter : substring matched case-insensitively against status.

    Returns a list of dicts (one per :class:`Entry` model) ranked by the
    hybrid score (vector · 0.7 + keyword · 0.3) over the inventory **only**
    when no multi-source filter is set. When any of the filter parameters
    is set we switch to the multi-source corpus + keyword backend (the
    vector index covers ARCH_INVENTORY only today — V2 extends it).
    """
    if top_k < 1:
        return []
    if top_k > 50:
        top_k = 50
    if not query or not query.strip():
        return []

    multi_source_mode = any(v is not None for v in (sources, file_pattern, freshness_days, status_filter))
    if multi_source_mode:
        results = _multi_source_search(
            query,
            top_k,
            sources=sources,
            file_pattern=file_pattern,
            freshness_days=freshness_days,
            status_filter=status_filter,
        )
        return [e.model_dump() for e in results]

    if docs_vector.is_fresh():
        results = _hybrid_search(query, top_k)
        if results:
            return [e.model_dump() for e in results]
        # Vector index present but rerank returned nothing (e.g. embedder
        # transient failure, all-zero scores). Fall through to keyword
        # rather than return an empty list — R33 graceful degradation.

    keyword_results = _keyword_search(query, top_k)
    return [e.model_dump() for e, _ in keyword_results]


def docs_reindex(force: bool = False) -> dict[str, Any]:
    """Rebuild the vector index from the current inventory. Read-only. Scope: ops.

    Args:
        force : skip the freshness check and rebuild unconditionally.
            Default ``False`` — a fresh index is returned untouched (cheap,
            ~50 ms : open SQLite, read meta).

    Returns a dict matching :class:`docs_vector.ReindexResult`. Raises if
    no embedder is available — install ``sentence-transformers`` to use
    the vector backend, or fall back to keyword-only by NOT calling this
    tool (the keyword path is the no-op default when no index exists).
    """
    result = docs_vector.build_or_refresh(force=force)
    return result.model_dump(mode="json")


# -- get -------------------------------------------------------------------


# Pattern for `## ID — title · refs · STATUT` headings — both at H2 (section
# level) and H1 (file level). The slice we read STOPS at the next header of
# the same level (H2 for H2-anchored entries, EOF for H1-anchored entries).
_H2_RE = re.compile(r"^##\s+([A-Z]+-\d+)\s+—\s+.+$")


def _slice_section(text: str, start_line: int) -> str:
    """Extract a section body from ``text``, starting at 1-indexed ``start_line``.

    Behaviour :

    * Header line is included.
    * If the header is an H2 (``## …``), the slice STOPS at the next H2
      heading. The horizontal rule `---` that separates HSP sections in
      ARCH_n8n_pipelines.md is included with the previous section (it's
      the visual divider it belongs to).
    * If the header is an H1 (``# Title``), the slice extends to EOF — a
      file-level entry is the whole file (legacy ARCHs).
    * Trailing whitespace is stripped from the returned body.
    """
    lines = text.splitlines()
    if start_line < 1 or start_line > len(lines):
        raise IndexError(f"start_line {start_line} out of range for {len(lines)} lines")
    header = lines[start_line - 1]
    is_h1 = header.startswith("# ") and not header.startswith("## ")
    out: list[str] = [header]
    i = start_line  # index of NEXT line (0-indexed list, 1-indexed start_line)
    while i < len(lines):
        line = lines[i]
        if not is_h1 and line.startswith("## ") and not line.startswith("### "):
            break
        out.append(line)
        i += 1
    body = "\n".join(out).rstrip()
    return body


def docs_get(id: str) -> dict[str, Any]:
    """Get the full body of one inventory section. Read-only. Scope: ops.

    Args:
        id : section identifier as it appears in ``ARCH_INVENTORY.md`` — e.g.
            ``DA-11``, ``HSP-3``, ``ARCH_AUTH``, ``KNOWN_PROBLEMS``.

    Returns a dict matching :class:`Section`. Raises :class:`KeyError` if the
    id is not found in the inventory and :class:`FileNotFoundError` if the
    source file referenced by the inventory has been moved without
    regenerating the index.
    """
    entries = load_inventory()
    match: Entry | None = next((e for e in entries if e.id == id), None)
    if match is None:
        raise KeyError(f"inventory entry {id!r} not found — regenerate ARCH_INVENTORY.md ?")

    # Source file is resolved relative to the repo root. `_repo_root()`
    # decouples this from the inventory location (now docs/reference/) while
    # still honouring the env override so the override-via-env tests work.
    repo_root = _repo_root()
    source = repo_root / match.file_path
    if not source.exists():
        raise FileNotFoundError(
            f"source file {match.file_path} (referenced by {id}) does not exist — "
            f"ARCH_INVENTORY.md is stale, regenerate it."
        )
    text = source.read_text(encoding="utf-8")
    body = _slice_section(text, match.line)

    section = Section(
        id=match.id,
        status=match.status,
        file_path=match.file_path,
        line=match.line,
        tags=list(match.tags),
        tldr=match.tldr,
        body=body,
    )
    return section.model_dump()


# -- find ------------------------------------------------------------------


def docs_find(
    status: str | None = None,
    tags: list[str] | None = None,
    file_glob: str | None = None,
) -> list[dict[str, Any]]:
    """Structured filter over the inventory. Read-only. Scope: ops.

    All criteria are AND-combined. Missing criteria mean "no constraint".

    Args:
        status : substring matched case-insensitively against the entry
            status. ``"LIVRÉ"`` matches both ``"LIVRÉ V0"`` and
            ``"LIVRÉ V1.1"``. Pass ``None`` to skip this filter.
        tags : list of tags ALL of which must appear in the entry's tags
            (intersection, case-insensitive exact match per tag).
        file_glob : ``fnmatch``-style glob on ``file_path`` — e.g.
            ``"docs/arch/*"`` (without the trailing ``.md``) or
            ``"docs/arch/*.md"``. Matched against the full repo-relative
            path.

    Returns a list of dicts (one per :class:`Entry`) sorted by ID alpha.
    """
    entries = load_inventory()
    out: list[Entry] = []
    needle_status = status.lower() if status else None
    needle_tags = [t.lower() for t in (tags or [])]

    for e in entries:
        if needle_status is not None and needle_status not in e.status.lower():
            continue
        if needle_tags:
            entry_tags = {t.lower() for t in e.tags}
            if not all(t in entry_tags for t in needle_tags):
                continue
        if file_glob is not None and not fnmatch.fnmatch(e.file_path, file_glob):
            continue
        out.append(e)

    out.sort(key=lambda e: e.id)
    return [e.model_dump() for e in out]


# -- list files ------------------------------------------------------------


def _categorise(path: str) -> str:
    """Map a repo-relative path to a coarse documentation bucket.

    Buckets (deterministic prefix match) :

    * ``docs/arch/``               → ``arch``
    * ``docs/known/``              → ``known``
    * ``docs/decisions/``          → ``decisions``
    * ``docs/audits/``             → ``audit``
    * ``docs/product/``            → ``product``
    * ``webservices/*/ARCH_*.md``  → ``service-arch``
    * ``batch/*/ARCH_*.md``        → ``batch-arch``
    * ``ratis_client/ARCH_*.md``   → ``client-arch``
    * anything else                → ``other``
    """
    if path.startswith("docs/arch/"):
        return "arch"
    if path.startswith("docs/known/"):
        return "known"
    if path.startswith("docs/decisions/"):
        return "decisions"
    if path.startswith("docs/audits/"):
        return "audit"
    if path.startswith("docs/product/"):
        return "product"
    if path.startswith("webservices/") and "/ARCH_" in path:
        return "service-arch"
    if path.startswith("batch/") and "/ARCH_" in path:
        return "batch-arch"
    if path.startswith("ratis_client/") and "/ARCH_" in path:
        return "client-arch"
    return "other"


def docs_list_files() -> list[dict[str, Any]]:
    """List every doc file referenced by the inventory. Read-only. Scope: ops.

    Returns one :class:`FileInfo` per distinct ``file_path``, with a
    coarse ``category`` and the number of entries that point at it.
    Sorted by category then by path.
    """
    entries = load_inventory()
    counts: dict[str, int] = {}
    for e in entries:
        counts[e.file_path] = counts.get(e.file_path, 0) + 1
    out = [FileInfo(path=p, category=_categorise(p), entries_count=n) for p, n in counts.items()]
    out.sort(key=lambda f: (f.category, f.path))
    return [f.model_dump() for f in out]


# -- session-context bridge ------------------------------------------------


_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-zÀ-ÿ0-9]+")
"""Regex used to split branch / message / path fragments into search tokens.

Hyphens, underscores, slashes — all treated as separators so a path like
``webservices/ratis_product_analyser/admin_ui`` becomes
``["webservices", "ratis", "product", "analyser", "admin", "ui"]``.
"""

# Branch/path tokens that don't help the search (project name, common verbs).
# Kept short — we don't want to over-engineer this. Adding more here is cheap.
_STOP_TOKENS: frozenset[str] = frozenset(
    {
        "ratis",
        "feat",
        "fix",
        "chore",
        "docs",
        "refactor",
        "test",
        "tests",
        "ci",
        "main",
        "master",
        "src",
        "lib",
        "pkg",
        "app",
        "tmp",
        "var",
        "etc",
        "claude",
        "session",
        "context",
        "users",
        "guillaume",
        "cursor",
        "worktrees",
    }
)


def _tokenise_for_query(text: str, *, min_len: int = 2) -> list[str]:
    """Split text on non-alphanumerics, lowercase, drop stopwords and short tokens.

    Tokens shorter than ``min_len`` are dropped (avoid `id`, `s`, etc.).
    We keep 2-char tokens because acronyms like ``ui`` / ``db`` / ``r2`` /
    ``pr`` carry real signal in our codebase.
    """
    if not text:
        return []
    toks = _TOKEN_SPLIT_RE.split(text.lower())
    return [t for t in toks if t and len(t) >= min_len and t not in _STOP_TOKENS]


def _infer_query_from_context(
    cwd: str | None,
    branch: str | None,
    user_message: str | None,
) -> str:
    """Combine cwd path components + branch tokens + user_message into a query.

    Order : user_message tokens FIRST (they carry the most signal when
    present), then branch, then path. Deduplicated, joined by spaces.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for source in (user_message, branch, cwd):
        for tok in _tokenise_for_query(source or ""):
            if tok not in seen:
                seen.add(tok)
                ordered.append(tok)
    return " ".join(ordered)


def docs_context_for_session(
    cwd: str,
    branch: str | None = None,
    user_message: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Compose a top-N semantic context nugget set for a fresh Claude Code session.

    Read-only. Scope: ops.

    Args:
        cwd : the session's working directory (absolute path). Used to
            infer the relevant subsystem (path components are tokenised).
        branch : current git branch — branch names like
            ``feat/session-context-bridge`` carry strong signal about
            "what is the user about to work on".
        user_message : optional first user message of the session, if
            available. Tokens from this string get priority.
        limit : number of nuggets to return (clamped to 1-20).

    Returns a dict :
        - ``query_inferred`` : the natural-language query we built
        - ``nuggets`` : list of Entry dicts (top-N hybrid hits)
        - ``indexed_at`` : ISO-8601 of when this call ran
        - ``sources_searched`` : list of source names searched
    """
    if limit < 1:
        limit = 1
    if limit > 20:
        limit = 20

    query = _infer_query_from_context(cwd, branch, user_message)
    sources_searched = [s.name for s in DEFAULT_SOURCES if s.enabled]

    if not query:
        return {
            "query_inferred": "",
            "nuggets": [],
            "indexed_at": datetime.now(UTC).isoformat(),
            "sources_searched": sources_searched,
        }

    nuggets = docs_search(
        query,
        top_k=limit,
        sources=sources_searched,
        freshness_days=30,
    )
    return {
        "query_inferred": query,
        "nuggets": nuggets,
        "indexed_at": datetime.now(UTC).isoformat(),
        "sources_searched": sources_searched,
    }


# -- registration ----------------------------------------------------------


_REGISTERED = False


def register_all() -> None:
    """Register the 4 docs tools into the module-level registry.

    Idempotent — pairs with `agent_mcp.server.load_builtin_tools()`.
    """
    global _REGISTERED
    if _REGISTERED and "docs_search" in TOOLS_REGISTRY:
        return

    if "docs_search" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(docs_search)
    if "docs_get" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(docs_get)
    if "docs_find" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(docs_find)
    if "docs_list_files" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(docs_list_files)
    if "docs_reindex" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(docs_reindex)
    if "docs_context_for_session" not in TOOLS_REGISTRY:
        register_tool(scope="ops")(docs_context_for_session)

    _REGISTERED = True


def _reset_for_tests() -> None:
    """Test-only — drop the idempotence flag so `register_all()` re-runs."""
    global _REGISTERED
    _REGISTERED = False
