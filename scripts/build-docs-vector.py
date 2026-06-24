#!/usr/bin/env python3
"""Build (or refresh) the docs-mcp vector index.

Usage:
    python scripts/build-docs-vector.py                  # rebuild if stale, else no-op
    python scripts/build-docs-vector.py --force          # rebuild unconditionally
    python scripts/build-docs-vector.py --skip-if-fresh  # explicit no-op (default behaviour)
    python scripts/build-docs-vector.py --silent         # no stdout, exit code only

Behaviour
---------
* Default mode = "rebuild if stale" : checks the SQLite index's
  ``inventory_mtime`` meta vs the current ``ARCH_INVENTORY.md`` mtime.
  If equal or newer, exits 0 immediately (~50 ms : open SQLite, read meta).
* Used by the session-start hook to keep the index fresh without paying
  the embedder cold-start when nothing changed.
* ``RATIS_DOCS_VECTOR_SKIP=1`` in env → exits 0 without doing anything.
  Hook in CI so the suite does not need to download the model.

References
----------
* CLAUDE.md R28 / R29 — agents must consult `ARCH_INVENTORY.md`, and this
  index makes `docs_search` semantically aware on top of it.
* ARCH_agent_mcp.md § Module 9 / phase D.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
# Add agent-mcp src to path so we don't depend on the workspace being
# installed (the hook runs `python scripts/...` directly, not via uv).
sys.path.insert(0, str(REPO_ROOT / "tools" / "agent-mcp" / "src"))


def _log(silent: bool, msg: str) -> None:
    if not silent:
        print(msg, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the freshness check and rebuild unconditionally.",
    )
    parser.add_argument(
        "--skip-if-fresh",
        action="store_true",
        help="(Default behaviour.) Exit 0 without doing anything when the "
        "index is already up-to-date with ARCH_INVENTORY.md.",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Suppress stdout — useful in session-start hooks. Errors still go to stderr.",
    )
    args = parser.parse_args()

    if os.environ.get("RATIS_DOCS_VECTOR_SKIP") == "1":
        _log(args.silent, "RATIS_DOCS_VECTOR_SKIP=1 → skipping vector index build.")
        return 0

    try:
        from agent_mcp.tools import docs_vector
    except ImportError as exc:
        # Module unavailable (workspace not installed) — fall back to a no-op
        # rather than crash the session-start hook.
        print(
            f"WARN: agent_mcp.tools.docs_vector not importable ({exc}) — skipping vector index build.",
            file=sys.stderr,
        )
        return 0

    if not args.force and docs_vector.is_fresh():
        _log(args.silent, "docs-vector-index is fresh — nothing to do.")
        return 0

    embedder = docs_vector.default_embedder()
    if embedder is None:
        print(
            "WARN: no embedder available (sentence-transformers not installed) — "
            "skipping vector index build. `docs_search` will fall back to keyword.",
            file=sys.stderr,
        )
        return 0

    started = time.monotonic()
    try:
        result = docs_vector.build_or_refresh(embedder=embedder, force=args.force)
    except Exception as exc:
        print(f"ERROR: vector index build failed : {exc}", file=sys.stderr)
        return 1
    elapsed_ms = int((time.monotonic() - started) * 1000)
    _log(
        args.silent,
        f"docs-vector-index : {result.entries_indexed} entries "
        f"({'skipped' if result.skipped else 'rebuilt'}) "
        f"with {result.model_name} in {elapsed_ms} ms.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
