#!/usr/bin/env bash
# SessionStart hook — semantic context injection.
#
# Pipes the (optional) Claude Code hook stdin payload into the Python
# wrapper, which queries `docs_context_for_session` in-process and prints
# a Markdown nugget block. The block is prepended to the session context.
#
# Discipline (R33 graceful degradation) :
#   - NO `set -e`: a hook failure must never block the session.
#   - Hard 5-second timeout : the corpus loader uses an in-process cache,
#     so the cold-path stays under that budget even on a stale index.
#   - All stderr is swallowed : noisy traces in the session transcript
#     would defeat the purpose of the bridge.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Resolve a timeout wrapper if one is available. Claude Code applies its
# own `"timeout": <s>` setting from settings.json, so the shell-side guard
# is a defence-in-depth — best-effort, optional. We use a single-string
# prefix (not a bash array) for compatibility with macOS bash 3.x.
TIMEOUT_PREFIX=""
if command -v timeout >/dev/null 2>&1; then
    TIMEOUT_PREFIX="timeout 8"
elif command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT_PREFIX="gtimeout 8"
fi

# Pass stdin (Claude Code hook payload) straight through. We prefer `uv run`
# so the script gets numpy / pydantic / agent-mcp deps from the workspace
# venv ; fall back to system python3 only if uv is missing (which means
# the wider repo workflow is also broken — we degrade gracefully via an
# HTML comment in that case).
if command -v uv >/dev/null 2>&1; then
    ${TIMEOUT_PREFIX} uv run --package ratis-agent-mcp python \
        "${REPO_ROOT}/scripts/hooks/inject-session-context.py" 2>/dev/null \
        || echo "<!-- session-context: hook timeout or uv python error -->"
elif command -v python3 >/dev/null 2>&1; then
    ${TIMEOUT_PREFIX} python3 \
        "${REPO_ROOT}/scripts/hooks/inject-session-context.py" 2>/dev/null \
        || echo "<!-- session-context: hook timeout or python3 error -->"
else
    echo "<!-- session-context: python3 not on PATH -->"
fi
