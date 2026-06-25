#!/usr/bin/env bash
# run-mypy.sh — the canonical static-type gate for the Ratis Python workspace.
#
# Why a wrapper (not a single `mypy .`) : this is a uv workspace where several
# packages ship top-level modules with the SAME name (each service has its own
# `main.py`, `routes/`, `services/`, `repositories/`). mypy cannot hold two
# modules with the same fully-qualified name in one run, so we type-check each
# package as its own root (its dir on MYPYPATH + --explicit-package-bases).
#
# Scope = the 5 FastAPI services + ratis_core + tools/agent-mcp. Migrations
# (alembic/) and tests are excluded via [tool.mypy] in pyproject.toml. Config
# lives there too — a strict gate with no disabled error codes.
#
# Usage :
#   ./scripts/run-mypy.sh            # check the whole scope, non-zero on any error
#   ./scripts/run-mypy.sh <path>...  # check only the given package paths
#
# Run via uv so the `typecheck` dependency-group (mypy + stubs) is available:
#   uv run --group typecheck ./scripts/run-mypy.sh
set -euo pipefail

# Package roots that make up the typed surface.
DEFAULT_TARGETS=(
  "ratis_core/ratis_core"
  "webservices/ratis_auth"
  "webservices/ratis_product_analyser"
  "webservices/ratis_list_optimiser"
  "webservices/ratis_rewards"
  "webservices/ratis_notifier"
  "tools/agent-mcp/src"
)

# Resolve repo root from this script's location so it works from any CWD.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ "$#" -gt 0 ]; then
  TARGETS=("$@")
else
  TARGETS=("${DEFAULT_TARGETS[@]}")
fi

rc=0
for path in "${TARGETS[@]}"; do
  echo "── mypy: $path"
  # Each package is its own root: put it on MYPYPATH and let mypy treat its
  # top-level dirs as package bases (so intra-package imports resolve and names
  # don't collide with the sibling services).
  if ! MYPYPATH="$path" mypy "$path" --explicit-package-bases; then
    rc=1
  fi
done

exit "$rc"
