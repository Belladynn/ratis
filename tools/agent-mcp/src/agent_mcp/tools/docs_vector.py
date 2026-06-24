"""Vector backend for `docs_search` — phase D agentic-docs.

The grep backend shipped in phase C (see :mod:`docs_tools`) does keyword
substring search. This module adds a **semantic** index : each inventory
entry is embedded with a sentence-transformer model and stored in
SQLite + ``sqlite-vec``. ``docs_search`` then runs a hybrid query
(cosine similarity + keyword) and re-ranks the two lists.

Design choices
--------------
* **Stored corpus per entry** : ``id + " " + " ".join(tags) + " " + tldr``.
  This is what an agent's natural-language query is going to match
  against — small enough to embed cheaply, semantically dense.
* **SQLite + sqlite-vec** : single-file portable index, zero ops, perfect
  for ~80 rows. We do not use the `vec0` virtual table (KNN-native) — at
  this scale a plain BLOB column with a Python-side cosine on numpy is
  trivially fast and avoids one more layer of abstraction. Easy to switch
  later if the corpus grows past a few thousand rows.
* **Embedder protocol** : `Embedder.embed(texts) -> np.ndarray[N, D]`.
  Two ready-made implementations :
    - :class:`SentenceTransformerEmbedder` (real, lazy-loads bge-m3 by
      default with miniLM fallback if bge-m3 fails to load) ;
    - :class:`HashEmbedder` (deterministic 16-D token-hash, for tests
      and as the "no model available" graceful path).
  Tests inject a `HashEmbedder` and never touch torch.
* **Freshness check** : the DB carries an `indexed_at` ISO-8601 timestamp.
  If the inventory file's mtime > indexed_at, the index is stale and the
  caller is expected to call :func:`build_or_refresh` (or just fall back
  to keyword-only).
* **No `db.commit()` discipline trap** (R02) — this is not the Ratis app
  Postgres ; SQLite autocommits when ``conn.commit()`` is called or
  inside `with conn:` blocks. We use the explicit ``with`` pattern.
* **Path resolution** : the SQLite DB lives at ``<repo>/.docs-vector-index.db``
  (gitignored). Override via env var ``RATIS_DOCS_VECTOR_DB_PATH`` (used
  by tests on tmpdir).

References
----------
* CLAUDE.md R29 (never full-read large docs) — this backend lets
  `docs_search` answer natural-language queries without reading any file.
* CLAUDE.md R33 (solution propre) — fallback paths are explicit, no
  silent swallow.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import numpy as np
from pydantic import BaseModel

from .docs_index import Entry, inventory_path, load_inventory

if TYPE_CHECKING:  # pragma: no cover — typing-only.
    from sentence_transformers import SentenceTransformer


# -- defaults & paths -----------------------------------------------------


DEFAULT_MODEL_NAME = "BAAI/bge-m3"
"""Default sentence-transformer model. ~600 MB on disk after the first
load. Free, multilingual, top of the FR/EN MTEB leaderboards (mid-2026).
Falls back to ``paraphrase-multilingual-MiniLM-L12-v2`` (~120 MB) if the
heavy model fails to load — see :func:`default_embedder`.
"""

FALLBACK_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

CORPUS_PER_ENTRY_TEMPLATE = "{id} {tags} {tldr}"
"""How an Entry is folded into a single text string for embedding."""


def _default_db_path() -> Path:
    """Repo-root SQLite index file (gitignored)."""
    return inventory_path().parent / ".docs-vector-index.db"


def db_path() -> Path:
    """Resolve the SQLite index path with env-var override (used by tests)."""
    override = os.environ.get("RATIS_DOCS_VECTOR_DB_PATH")
    if override:
        return Path(override).expanduser()
    return _default_db_path()


# -- embedder protocol ----------------------------------------------------


class Embedder(Protocol):
    """Tiny protocol so :func:`build_or_refresh` and :func:`search` don't
    depend on a concrete model implementation."""

    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:  # pragma: no cover — protocol.
        """Return an (N, D) float32 array of L2-normalised vectors."""
        ...


_TOKEN_RE = re.compile(r"[A-Za-z0-9À-ÿ]+")


def _tokenise(text: str) -> list[str]:
    """Lowercase token split, accent-preserving (good enough for hash embedder)."""
    return [t.lower() for t in _TOKEN_RE.findall(text)]


@dataclass(slots=True)
class HashEmbedder:
    """Deterministic embedder used as the test stub AND the no-model fallback.

    Algorithm : for each token in the text, hash it modulo ``dim`` and
    accumulate +1 in that coordinate. L2-normalise the result. Two
    sentences sharing many tokens get high cosine similarity ; disjoint
    sentences get cosine ≈ 0. Cheap, deterministic, no dependency.

    NOT a true semantic embedder — it does not handle synonyms or
    paraphrase. Use a real model in production via :func:`default_embedder`.
    """

    dim: int = 16

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for tok in _tokenise(text):
                # hashlib.sha256 is deterministic across platforms and PYTHONHASHSEED
                digest = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
                idx = digest % self.dim
                out[i, idx] += 1.0
            norm = np.linalg.norm(out[i])
            if norm > 0:
                out[i] /= norm
        return out


class SentenceTransformerEmbedder:
    """Real sentence-transformer wrapper — lazy-loads on the first embed().

    Pass ``model_name="..."`` to pin a specific model ; default is
    :data:`DEFAULT_MODEL_NAME`. The first call downloads ~600 MB ; later
    calls reuse the on-disk cache.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME) -> None:
        self.model_name = model_name
        self._model: SentenceTransformer | None = None
        self.dim = 0  # populated at first embed()

    def _load(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name)
        # Probe to learn the dim — one empty embed is cheap.
        probe = self._model.encode(["_probe_"], normalize_embeddings=True)
        self.dim = int(probe.shape[1])

    def embed(self, texts: list[str]) -> np.ndarray:
        self._load()
        assert self._model is not None
        arr = self._model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return arr.astype(np.float32)


def default_embedder() -> Embedder | None:
    """Return a production-grade embedder, or ``None`` if unavailable.

    Order :

    1. Try :class:`SentenceTransformerEmbedder` with the heavy bge-m3 model.
    2. On any failure (sentence-transformers missing, model download fail,
       OOM, etc.), try the lighter miniLM fallback.
    3. If both fail, return ``None`` — the caller falls back to keyword-only.

    Callers that want the deterministic stub for testing should pass a
    :class:`HashEmbedder` directly to :func:`build_or_refresh` /
    :func:`search` rather than calling this helper.
    """
    try:
        import sentence_transformers  # noqa: F401 — availability probe.
    except ImportError:
        return None
    # sentence_transformers is importable — try the heavy model first.
    try:
        return SentenceTransformerEmbedder(model_name=DEFAULT_MODEL_NAME)
    except Exception:
        try:
            return SentenceTransformerEmbedder(model_name=FALLBACK_MODEL_NAME)
        except Exception:
            return None


# -- typed reindex result -------------------------------------------------


class ReindexResult(BaseModel):
    """Returned by :func:`build_or_refresh` and the `docs_reindex` MCP tool."""

    entries_indexed: int
    """Number of inventory entries that were embedded into the index."""

    skipped: bool
    """True when the index was already fresh and no work was done."""

    db_path: Path
    """Absolute path of the SQLite file written (or skipped)."""

    indexed_at: str
    """ISO-8601 UTC timestamp of the (re)index run, or of the existing
    fresh index when ``skipped`` is True."""

    model_name: str
    """Name of the embedder used (e.g. ``BAAI/bge-m3`` or ``HashEmbedder``)."""


# -- DB schema ------------------------------------------------------------


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS doc_embeddings (
    id           TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    line         INTEGER NOT NULL,
    tags         TEXT NOT NULL,
    tldr         TEXT NOT NULL,
    embedding    BLOB NOT NULL,
    dim          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS doc_meta (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL
);
"""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA_SQL)
    return conn


def _read_meta(conn: sqlite3.Connection, key: str) -> str | None:
    cur = conn.execute("SELECT value FROM doc_meta WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else None


def _write_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO doc_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# -- freshness ------------------------------------------------------------


def _inventory_mtime() -> float:
    """mtime of the ARCH_INVENTORY.md file. Raises if missing."""
    return inventory_path().stat().st_mtime


def is_fresh() -> bool:
    """Return ``True`` if the vector DB exists AND its ``indexed_at`` is >=
    the inventory's mtime.

    Used both by the session-start hook (skip rebuild when fresh) and by
    :func:`docs_tools.docs_search` (decide whether to use the vector
    backend or fall straight to keyword).
    """
    target = db_path()
    if not target.exists():
        return False
    try:
        inv_mtime = _inventory_mtime()
    except FileNotFoundError:
        return False
    try:
        with _connect(target) as conn:
            stored = _read_meta(conn, "inventory_mtime")
    except sqlite3.DatabaseError:
        return False
    if stored is None:
        return False
    try:
        return float(stored) >= inv_mtime
    except ValueError:
        return False


# -- build / refresh ------------------------------------------------------


def _entry_corpus(entry: Entry) -> str:
    """Fold one Entry into the text we actually embed.

    We deliberately exclude ``file_path`` (paths leak structure, not
    semantics) and ``status`` (too noisy : 'LIVRÉ' would dominate).
    """
    return CORPUS_PER_ENTRY_TEMPLATE.format(
        id=entry.id,
        tags=" ".join(entry.tags),
        tldr=entry.tldr,
    )


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


def build_or_refresh(
    *,
    embedder: Embedder | None = None,
    force: bool = False,
) -> ReindexResult:
    """(Re)build the SQLite vector index from the current inventory.

    Args:
        embedder : an :class:`Embedder`. Defaults to :func:`default_embedder`.
            If both the default and fallback fail (no torch, no model on
            disk), a :class:`RuntimeError` is raised — callers that need a
            graceful fallback should detect ``embedder is None`` themselves.
        force : skip the freshness check and rebuild unconditionally. Used
            by the ``docs_reindex(force=True)`` MCP tool.

    Returns a :class:`ReindexResult`. ``skipped=True`` when the index was
    already fresh — no rows were rewritten.
    """
    target = db_path()
    embedder = embedder or default_embedder()
    if embedder is None:
        raise RuntimeError(
            "No embedder available — install `sentence-transformers` "
            "(`uv add --optional vector sentence-transformers`) or pass a "
            "HashEmbedder for tests."
        )

    if not force and is_fresh():
        # Surface the existing index without touching disk.
        with _connect(target) as conn:
            indexed_at = _read_meta(conn, "indexed_at") or _now_iso()
            model_name = _read_meta(conn, "model_name") or type(embedder).__name__
            count = conn.execute("SELECT COUNT(*) FROM doc_embeddings").fetchone()[0]
        return ReindexResult(
            entries_indexed=int(count),
            skipped=True,
            db_path=target,
            indexed_at=indexed_at,
            model_name=model_name,
        )

    entries = load_inventory(force=True)
    corpus = [_entry_corpus(e) for e in entries]
    if not entries:
        # Empty inventory : still write meta so freshness checks work.
        with _connect(target) as conn:
            conn.execute("DELETE FROM doc_embeddings")
            _write_meta(conn, "indexed_at", _now_iso())
            _write_meta(conn, "inventory_mtime", str(_inventory_mtime()))
            _write_meta(conn, "model_name", type(embedder).__name__)
            conn.commit()
        return ReindexResult(
            entries_indexed=0,
            skipped=False,
            db_path=target,
            indexed_at=_now_iso(),
            model_name=type(embedder).__name__,
        )

    vectors = embedder.embed(corpus)
    if vectors.ndim != 2 or vectors.shape[0] != len(entries):
        raise RuntimeError(f"Embedder returned shape {vectors.shape}, expected ({len(entries)}, D).")
    dim = int(vectors.shape[1])

    indexed_at = _now_iso()
    with _connect(target) as conn:
        # Wipe & rewrite — simpler than diffing for 80 rows.
        conn.execute("DELETE FROM doc_embeddings")
        conn.executemany(
            "INSERT INTO doc_embeddings(id, status, file_path, line, tags, tldr, embedding, dim) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    e.id,
                    e.status,
                    e.file_path,
                    e.line,
                    " ".join(e.tags),
                    e.tldr,
                    vectors[i].astype(np.float32).tobytes(),
                    dim,
                )
                for i, e in enumerate(entries)
            ],
        )
        _write_meta(conn, "indexed_at", indexed_at)
        _write_meta(conn, "inventory_mtime", str(_inventory_mtime()))
        _write_meta(conn, "model_name", type(embedder).__name__)
        _write_meta(conn, "dim", str(dim))
        conn.commit()

    return ReindexResult(
        entries_indexed=len(entries),
        skipped=False,
        db_path=target,
        indexed_at=indexed_at,
        model_name=type(embedder).__name__,
    )


# -- search ---------------------------------------------------------------


def search(
    query: str,
    *,
    top_k: int = 20,
    embedder: Embedder | None = None,
) -> list[tuple[Entry, float]]:
    """Cosine-similarity ranked search over the vector index.

    Returns an empty list when no index is present — the caller (typically
    :func:`docs_tools.docs_search`) treats that as "fall back to keyword".

    Scores are cosine similarities in ``[-1, 1]`` clamped to ``[0, 1]``
    (negative similarity in a relevance context means "irrelevant", so we
    floor it). Callers can compare scores across runs as long as the
    embedder is the same.
    """
    if top_k < 1:
        return []
    target = db_path()
    if not target.exists():
        return []
    embedder = embedder or default_embedder()
    if embedder is None:
        return []

    try:
        query_vec = embedder.embed([query])[0]
    except Exception:
        return []
    q = np.asarray(query_vec, dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        return []

    with _connect(target) as conn:
        rows = conn.execute(
            "SELECT id, status, file_path, line, tags, tldr, embedding, dim FROM doc_embeddings"
        ).fetchall()
    if not rows:
        return []

    scored: list[tuple[Entry, float]] = []
    for row_id, status, file_path, line, tags_str, tldr, blob, dim in rows:
        vec = np.frombuffer(blob, dtype=np.float32)
        if vec.shape[0] != dim or vec.shape[0] != q.shape[0]:
            # Schema/dim mismatch — most likely a stale index built with a
            # different embedder. Skip the row rather than crash ; the next
            # `build_or_refresh` will fix it.
            continue
        denom = q_norm * float(np.linalg.norm(vec))
        if denom == 0 or math.isnan(denom):
            sim = 0.0
        else:
            sim = float(np.dot(q, vec) / denom)
        sim = max(0.0, min(1.0, sim))
        entry = Entry(
            id=row_id,
            status=status,
            file_path=file_path,
            line=line,
            tags=tags_str.split() if tags_str else [],
            tldr=tldr,
        )
        scored.append((entry, sim))

    scored.sort(key=lambda pair: (-pair[1], pair[0].id))
    return scored[:top_k]


# -- test reset -----------------------------------------------------------


def _reset_for_tests() -> None:
    """No module-level cache today, but kept symmetric with `docs_tools._reset_for_tests`."""
    # Reserved for future per-process caches.
    return
