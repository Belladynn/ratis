#!/usr/bin/env python3
"""SessionStart hook — semantic context injection.

Queries :func:`agent_mcp.tools.docs_tools.docs_context_for_session` with the
current ``cwd`` + ``git branch`` and prints a Markdown nugget block on
stdout. Claude Code's SessionStart hook protocol prepends stdout to the
session context, so the nuggets appear as ambient knowledge for the first
turn.

Design choices
--------------
* **In-process** rather than ``agent-mcp call`` to avoid the
  ``MCP_AUTH_TOKEN`` round-trip — this is a local read-only call, not a
  privilege-escalation surface. The hook runs under the operator's UID and
  reads files the operator already owns.
* **Silent fail-fast** : every error path falls back to a single comment
  line on stdout. A broken hook must NEVER block a session — that's the
  R33 graceful degradation contract.
* **Bounded runtime** : the hook is wrapped by Claude Code's 5-second
  hook timeout. The corpus loader uses the 60-second cache so subsequent
  session starts are cheap.

References
----------
* ``.claude/settings.json`` SessionStart array
* ``ARCH_agent_mcp.md`` § Module 9
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Ensure agent-mcp is importable without uv — falls back gracefully if not.
sys.path.insert(0, str(REPO_ROOT / "tools" / "agent-mcp" / "src"))


def _current_branch(cwd: Path) -> str:
    """Return the current git branch, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:  # noqa: S110 — hook must NEVER block ; branch detection is best-effort.
        pass
    return ""


def _format_nuggets(payload: dict) -> str:
    """Turn the docs_context_for_session payload into a Markdown block.

    Empty payloads collapse to a single comment line — the hook still emits
    something so the operator can confirm "the hook ran" via the session
    transcript.
    """
    nuggets = payload.get("nuggets") or []
    query = payload.get("query_inferred") or "(empty)"
    indexed_at = payload.get("indexed_at") or "(unknown)"
    if not nuggets:
        return f"<!-- session-context: no nuggets matched query '{query}' (indexed_at={indexed_at}) -->"
    lines: list[str] = [
        "## Session context (auto-injected from doc corpus)",
        "",
        f"_Top {len(nuggets)} matches for inferred query :_ `{query}`",
        "",
    ]
    for n in nuggets:
        nid = n.get("id", "?")
        status = n.get("status", "")
        source = n.get("source", "")
        path = n.get("file_path", "")
        line = n.get("line", 1)
        tldr = n.get("tldr", "")
        tags = " ".join(n.get("tags") or [])
        path_ref = f"{path}:{line}" if path else "(no path)"
        lines.append(f"- **{nid}** · `{source}` · {status} · `{path_ref}`{'  ·  tags: ' + tags if tags else ''}")
        if tldr:
            lines.append(f"  > {tldr}")
    lines.append("")
    lines.append(f"<!-- indexed_at={indexed_at} -->")
    return "\n".join(lines)


def main() -> int:
    # Hook stdin payload — we accept whatever shape Claude Code sends but
    # only need `cwd`. If parsing fails we fall back to the actual cwd.
    cwd: Path
    try:
        raw = sys.stdin.read()
        payload_in = json.loads(raw) if raw.strip() else {}
        cwd_hint = payload_in.get("cwd") or ""
        if cwd_hint:
            cwd = Path(cwd_hint)
        else:
            cwd = Path.cwd()
    except Exception:
        cwd = Path.cwd()

    branch = _current_branch(cwd)

    try:
        # Local import — keeps this script importable for unit testing
        # without requiring the agent-mcp package on PATH first.
        from agent_mcp.tools.docs_tools import docs_context_for_session

        result = docs_context_for_session(
            cwd=str(cwd),
            branch=branch or None,
            limit=5,
        )
    except Exception as exc:
        print(f"<!-- session-context: hook error : {exc!r} -->")
        return 0

    print(_format_nuggets(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
